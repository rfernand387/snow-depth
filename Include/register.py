# import necessary packages
import cv2
import numpy as np
import json
import tqdm
from matplotlib import pyplot as plt
import os
import time

def register(img, name, template, template_reduced_noise, img_apply, debug,
    debug_directory_registered, debug_directory_matches, dataset, dataset_enabled,
    NUM_STD_DEV, max_mean_squared_error, MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING,
    params, misc_params):
    """
    Function to align an image to a provided template

    Keyword arguments:
    img -- grayscale denoised image to be used for feature based alignment
    name -- image file name
    template -- equalized template image to be used in ECC alignment
    template_reduced_noise -- denoised template image to be used in feature based alignment
    img_apply -- equalized image to be aligned and used in later steps of algorithm
    debug -- bool flag indicating whether output images should be saved
    debug_directory_registered -- directory where registered images should be written
    debug_directory_matches -- directory where match images should be written
    dataset -- robust registration dataset
    dataset_enabled -- bool flag indicating whether robust dataset should be used
    NUM_STD_DEV -- number of standard deviations away from dataset mean the MSE
        of the affine matrix can be
    max_mean_squared_error -- calculated maximum MSE given the transformation parameters
        inputted by the user during template creation
    MAX_ROTATION -- maximum allowable rotation
    MAX_TRANSLATION -- maximum allowable translation
    MAX_SCALING -- maximum allowable image scaling
    params -- registration parameters (e.g. ECC thresholds, ORB features, etc.)
    misc_params -- contains bool flag indicating whether ssim comparison should
        be performed
    """

    # flags for whether image was aligned
    ORB_aligned_flag = False
    ECC_aligned_flag = False

    # detect ORB features and compute descriptors
    orb = cv2.ORB_create(nfeatures=params[0])
    kp1, desc1 = orb.detectAndCompute(img, None)
    kp2, desc2 = orb.detectAndCompute(template_reduced_noise, None)

    # create brute-force matcher object and match descriptors
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(desc1, desc2)

    # sort matches by score
    matches = sorted(matches, key=lambda x: x.distance)

    # filter out poor matches based on distance between endpoints
    filteredMatches = list()
    height, width = template.shape
    MAX_DISTANCE = params[5] / 100.0
    thresholdDist = MAX_DISTANCE * np.sqrt(np.square(height) + np.square(width))
    for m in matches:
        # extract endpoints
        pt1 = kp1[m.queryIdx].pt
        pt2 = kp2[m.trainIdx].pt

        # calculate distance between endpoints and filter
        dist = np.sqrt(np.square(pt1[0] - pt2[0]) + np.square(pt1[1] - pt2[1]))
        if dist < thresholdDist:
            filteredMatches.append(m)

        # only keep 1000 filtered matches
        if len(filteredMatches) >= 1000: break

    # draw top matches
    imgMatches = cv2.drawMatches(img, kp1, template, kp2, filteredMatches, None, flags=2)

    # extract location of good matches
    points1 = np.zeros((len(filteredMatches), 2), dtype=np.float32)
    points2 = np.zeros((len(filteredMatches), 2), dtype=np.float32)

    for i, match in enumerate(filteredMatches):
        points1[i, :] = kp1[match.queryIdx].pt
        points2[i, :] = kp2[match.trainIdx].pt

    # determine affine 2D transformation using RANSAC robust method
    # if feature based registration selected
    if((params[6] == 0 or params[6] == 1) and ((len(filteredMatches)>0) and (len(points1) == len(points2)))):
        affine_matrix = cv2.estimateAffine2D(points1, points2, method = cv2.RANSAC,
            refineIters=20)[0]
    else:
        # use matrix no transformation matrix
        affine_matrix = np.eye(2, 3, dtype=np.float32)

    # set registered images to original images
    # will be warped if affine matrix is within spec
    imgReg = img_apply
    imgRegGray = cv2.cvtColor(img_apply, cv2.COLOR_BGR2GRAY)

    # get mean squared error between affine matrix and zero matrix
    zero_matrix = np.zeros((2,3), dtype=np.float32)
    mean_squared_error = np.sum(np.square(abs(affine_matrix) - zero_matrix))

    # update dataset
    # if dataset isn't enabled, append mean_squared_error to dataset or if
    # robust registration isn't in use
    if((not dataset_enabled or not params[7]) and validTransform(MAX_ROTATION, MAX_TRANSLATION,
        MAX_SCALING, affine_matrix) and params[6] != 2):
        # apply registration
        imgReg = cv2.warpAffine(img_apply, affine_matrix, (width, height))
        imgRegGray = cv2.cvtColor(imgReg, cv2.COLOR_BGR2GRAY)
        ORB_aligned_flag = True

    # if dataset is enabled, compare matrix to mean
    elif validTransform(MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING, affine_matrix) \
         and params[6] != 2:
        # get mean and standard deviation from dataset
        mean = dataset[0][0]
        std_dev = dataset[0][1]

        # if mean mean_squared_error is within defined range
        if (mean_squared_error <= (mean+(std_dev*NUM_STD_DEV))):
            # apply registration
            imgReg = cv2.warpAffine(img_apply, affine_matrix, (width, height))
            imgRegGray = cv2.cvtColor(imgReg, cv2.COLOR_BGR2GRAY)
            ORB_aligned_flag = True

    # write matches to debug directory
    if(debug):
        filename, ext = os.path.splitext(name)
        cv2.imwrite(debug_directory_matches + name, imgMatches)
        cv2.imwrite(debug_directory_matches + filename + "-ORB" + ext, imgReg) # ORB aligned image

    # calculate ssim of ORB registered image
    if misc_params[1]: # if user is using ssim comparison
        from skimage.measure import compare_ssim as ssim
        ORB_ssim = ssim(imgRegGray, template)

        # create copy of ORB registered image
        imgRegCopy = imgReg.copy()

    # remove black space from image for ECC registration
    y_transform = affine_matrix[1][2]
    x_transform = affine_matrix[0][2]

    # upper and lower coordinates
    upperX = 0
    upperY = 0
    lowerX = width
    lowerY = height

    # update coordinates based on affine matrix
    if y_transform > 0:
        upperY += y_transform
    else:
        lowerY += y_transform

    if x_transform > 0:
        upperX += x_transform
    else:
        lowerX += x_transform

    # crop registered image and template
    imgCrop = imgRegGray[int(upperY):int(lowerY), int(upperX):int(lowerX)]
    templateCrop = template[int(upperY):int(lowerY), int(upperX):int(lowerX)]

    # write to matches directory
    if debug:
        cv2.imwrite(debug_directory_matches + filename + "-crop" + ext, imgCrop)
        cv2.imwrite(debug_directory_matches + filename + "-template-crop" + ext, templateCrop)

    # define ECC motion model
    warp_mode = cv2.MOTION_AFFINE
    warp_matrix = np.eye(2, 3, dtype=np.float32)

    # specify the number of iterations and threshold
    number_iterations = int(params[2]) if ORB_aligned_flag else int(params[4])
    termination_thresh = 1.0 / pow(10, params[1]) if ORB_aligned_flag else 1.0 / pow(10, params[3])
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, number_iterations,  termination_thresh)

    # Flag to indicate if ECC failed
    ECC_Failed_Flag = False

    # run ECC algorithm (results are stored in warp matrix)
    # if ECC image registration selected
    if(params[6] == 0 or params[6] == 2):
        # run ECC registration on ORB aligned image
        try:
            warp_matrix = cv2.findTransformECC(templateCrop, imgCrop, warp_matrix, warp_mode, criteria)[1]
        except:
            # if ECC fails
            if ORB_aligned_flag: # try again on original images
                try:
                    warp_matrix = cv2.findTransformECC(template, imgRegGray, warp_matrix, warp_mode, criteria)[1]
                except:
                    # set flags to False
                    ORB_aligned_flag = False
                    ECC_Failed_Flag = True
            else: # if images weren't ORB aligned
                # set flags to False
                ORB_aligned_flag = False
                ECC_Failed_Flag = True

    # compare warp matrix to data set
    mean_squared_error_ecc = np.sum(np.square(abs(warp_matrix) - zero_matrix))

    # only check if dataset enabled
    if(dataset_enabled and validTransform(MAX_ROTATION, MAX_TRANSLATION,
        MAX_SCALING, warp_matrix) and not ECC_Failed_Flag):
        # get mean and standard deviation from dataset
        mean = dataset[0][0]
        std_dev = dataset[0][1]

        # align image if warp is within spec
        if (mean_squared_error_ecc <= (mean+(std_dev*NUM_STD_DEV)) and params[6] != 1):
            # align image
            imgECCAligned = cv2.warpAffine(imgReg, warp_matrix, (width,height), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
            ECC_aligned_flag = True

        # else use ORB registered image
        else:
            imgECCAligned = imgReg

    # align image if dataset not enabled
    elif mean_squared_error_ecc <= max_mean_squared_error and params[6] != 1 and \
        not dataset_enabled and validTransform(MAX_ROTATION, MAX_TRANSLATION,
        MAX_SCALING, warp_matrix) and not ECC_Failed_Flag:
        # align image
        imgECCAligned = cv2.warpAffine(imgReg, warp_matrix, (width,height), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
        ECC_aligned_flag = True

    # doesn't meet criteria
    else: imgECCAligned = imgReg

    # calculate ssim of ECC registered image
    if misc_params[1]: # if user is using ssim comparison
        ECC_ssim = ssim(cv2.cvtColor(imgECCAligned.copy(), cv2.COLOR_BGR2GRAY), template)

        # use ORB image if higher ssim
        if ORB_ssim > ECC_ssim:
            imgECCAligned = imgRegCopy

    # increase saturation of registered image to resemble input image
    h, s, v = cv2.split(cv2.cvtColor(imgECCAligned, cv2.COLOR_BGR2HSV).astype(np.float32))
    s *= 2.0 # double saturation
    s = np.clip(s, 0, 255)

    # merge channels
    hsv_merge = cv2.merge((h, s, v))
    imgECCAligned = cv2.cvtColor(hsv_merge.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # only if image was aligned (is not the same as input image)
    if(ORB_aligned_flag or ECC_aligned_flag):
        # write registered image to debug directory
        if debug:
            cv2.imwrite(debug_directory_registered + name, imgECCAligned)

        # return registered image and parameters
        return (imgECCAligned, name, mean_squared_error, ORB_aligned_flag,
            affine_matrix.tolist(), ECC_aligned_flag, warp_matrix.tolist())

    # return None if not aligned
    else: return (None, name, mean_squared_error, ORB_aligned_flag,
        affine_matrix.tolist(), ECC_aligned_flag, warp_matrix.tolist())

def validTransform(MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING, matrix):
    """
    Function to determine whether a transformation is valid based on parameters

    Keyword arguments:
    MAX_ROTATION -- maximum allowable rotation
    MAX_TRANSLATION -- maximum allowable translation
    MAX_SCALING -- maximum allowable image scaling
    matrix -- affine matrix to verify
    """

    # calculate range for alpha
    scale = abs(MAX_SCALING) / 100.0
    alpha_low = (1-scale) * np.cos(np.deg2rad(MAX_ROTATION))
    alpha_high = (1+scale) * np.cos(np.deg2rad(0))

    # verify affine matrix elements are within range
    if(alpha_low <= abs(matrix[0][0]) and abs(matrix[0][0]) <= alpha_high and
        alpha_low <= abs(matrix[1][1]) and abs(matrix[1][1]) <= alpha_high and
        abs(matrix[0][2]) < MAX_TRANSLATION and abs(matrix[1][2]) < MAX_TRANSLATION):
        return True
    else:
        return False

def getMaxError(MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING):
    """
    Function to determine the maximum mean squared error for non-initialized dataset

    Keyword arguments:
    MAX_ROTATION -- maximum allowable rotation
    MAX_TRANSLATION -- maximum allowable translation
    MAX_SCALING -- maximum allowable image scaling
    """

    # adjust scaling from % to absolute
    MAX_SCALING = abs(MAX_SCALING) / 100.0

    # create zero matrix
    zero_matrix = np.zeros((2, 3), dtype=np.float32)

    # create affine matrix according to specified rotation, translation and scale
    alpha = MAX_SCALING * np.cos(np.deg2rad(MAX_ROTATION))
    beta = MAX_SCALING * np.sin(np.deg2rad(MAX_ROTATION))
    affine_transform_matrix = np.array([
        [alpha, beta, MAX_TRANSLATION],
        [-beta, alpha, MAX_TRANSLATION]])

    # get maximum mean squared error
    return np.sum(np.square(abs(affine_transform_matrix) - zero_matrix))

def updateDataset(dataset, MSE_vals, dataset_enabled):
    """
    Function to update the dataset given the MSE values for a set of images

    Keyword arguments:
    dataset -- dataset to be updated
    MSE_vals -- list of mean squared error values calculated from input images
    dataset_enabled -- bool flag indicating whether dataset has been initialized
    """

    # if dataset is enabled
    if dataset_enabled:
        # get mean and standard deviation from dataset
        mean = dataset[0][0]
        std_dev = dataset[0][1]

        # iterate through mean squared error values
        for x in MSE_vals:
            num_vals_dataset = dataset[0][2]
            new_vals_dataset = num_vals_dataset + 1
            new_mean = ((mean * num_vals_dataset) + x) / new_vals_dataset
            new_std_dev = np.sqrt(pow(std_dev, 2) + ((((x - mean) * (x - new_mean)) - \
                pow(std_dev, 2)) / new_vals_dataset))
            dataset = np.array([[new_mean, new_std_dev, new_vals_dataset], []])

    # else add values to dataset
    else:
        for x in MSE_vals:
            dataset[1].append(x)

    # return updated dataset
    return dataset

# align a set of images to the provided template
def alignImages(imgs, template, template_reduced_noise, img_names, imgs_apply,
    debug_directory_registered, debug_directory_matches, debug, dataset,
    dataset_enabled, MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING, NUM_STD_DEV, params,
    imageSummary, misc_params):
    """
    Function to align a set of images to the provided template

    Keyword arguments:
    imgs -- input images to be aligned
    template -- equalized template image to be used in ECC alignment
    template_reduced_noise -- denoised template image to be used in feature based alignment
    img_names -- list of corresponding image file names
    imgs_apply -- list of equalized images to be aligned and used later in algorithm
    debug_directory_registered -- directory where registered images should be written
    debug_directory_matches -- directory where match images should be written
    debug -- bool flag indicating whether output images should be saved
    dataset -- robust registration dataset
    dataset_enabled -- bool flag indicating whether robust dataset should be used
    MAX_ROTATION -- maximum allowable rotation
    MAX_TRANSLATION -- maximum allowable translation
    MAX_SCALING -- maximum allowable image scaling
    NUM_STD_DEV -- number of standard deviations away from dataset mean the MSE
        of the affine matrix can be
    params -- registration parameters (e.g. ECC thresholds, ORB features, etc.)
    imageSummary -- dictionary containing information about each run
    misc_params -- contains bool flag indicating whether ssim comparison should
        be performed
    """

    # counter for successful ORB and ECC registrations
    validORB = 0
    validECC = 0

    # get maximum mean squared error
    max_mean_squared_error = getMaxError(MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING)

    # create output list for images
    registeredImages = list()
    images_names_registered = list()

    # contains output data for JSON file
    registration_output = dict()

    # list to hold new MSE values
    MSE_vals = list()

    # iterator
    count = 0

    # iterate through images
    for img in tqdm.tqdm(imgs):
        # get application image
        img_apply = imgs_apply[count]

        # align image
        output = register(img, img_names[count], template, template_reduced_noise,
            img_apply, debug, debug_directory_registered, debug_directory_matches,
            dataset, dataset_enabled, NUM_STD_DEV, max_mean_squared_error,
            MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING, params, misc_params)

        # if image was aligned
        if output[0] is not None:
            # unpack return
            (imgAligned, name, MSE, ORBFlag, ORBMatrix, ECCFlag, ECCMatrix) = output

            # add data to JSON output
            registration_output[name] = {
                "ORB Aligned": ORBFlag,
                "ORB Matrix": ORBMatrix,
                "ECC Aligned": ECCFlag,
                "ECC Matrix": ECCMatrix
            }

            # add images to list
            registeredImages.append(imgAligned)
            images_names_registered.append(name)

            # update registration lists
            validORB += ORBFlag
            validECC += ECCFlag

            # add MSE to list
            MSE_vals.append(MSE)

            # add to individual summary
            imageSummary[name]["Registered"] = True
            imageSummary[name]["ORB Registered"] = ORBFlag
            imageSummary[name]["ORB Matrix"] = np.round(ORBMatrix, 2)
            imageSummary[name]["ECC Registered"] = ECCFlag
            imageSummary[name]["ECC Matrix"] = np.round(ECCMatrix, 2)

        # image wasn't registered
        else:
            # unpack return
            (_, name, MSE, ORBFlag, ORBMatrix, ECCFlag, ECCMatrix) = output

            # add data to JSON output
            registration_output[name] = {
                "ORB Aligned": ORBFlag,
                "ORB Matrix": ORBMatrix,
                "ECC Aligned": ECCFlag,
                "ECC Matrix": ECCMatrix
            }

            # add to individual summary
            imageSummary[name]["Registered"] = False
            imageSummary[name]["ORB Registered"] = ORBFlag
            imageSummary[name]["ORB Matrix"] = np.round(ORBMatrix, 2)
            imageSummary[name]["ECC Registered"] = ECCFlag
            imageSummary[name]["ECC Matrix"] = np.round(ECCMatrix, 2)

        # increment iterator
        count += 1

    # update dataset
    if params[7]: # update dataset if using robust registration
        print("Updating Dataset...")
        dataset = updateDataset(dataset, MSE_vals, dataset_enabled)
    avg_MSE = sum(MSE_vals) / len(MSE_vals) if len(MSE_vals) > 0 else 0

    # if in debugging mode
    if(debug):
        # output JSON file
        file = open(debug_directory_registered + 'registered.json', 'w')
        json.dump(registration_output, file, sort_keys=True, indent=4, separators=(',', ': '))

    # return list of registered images
    return registeredImages, dataset, images_names_registered, [validORB, validECC, avg_MSE], imageSummary

# unpack arguments of parallel pool tasks
def unpackArgs(args):
    """
    Function to unpack arguments explicitly and call register function

    Keyword arguments:
    args -- function arguments passed by parallel registration function
    """
    return register(*args)

def alignImagesParallel(pool, imgs, template, template_reduced_noise, img_names,
     imgs_apply, debug_directory_registered, debug_directory_matches, debug, dataset,
    dataset_enabled, MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING, NUM_STD_DEV, params,
    imageSummary, misc_params):
    """
    Function to align a set of images to the provided template using a parallel pool

    Keyword arguments:
    pool -- parallel pool to be used for computing
    imgs -- input images to be aligned
    template -- equalized template image to be used in ECC alignment
    template_reduced_noise -- denoised template image to be used in feature based alignment
    img_names -- list of corresponding image file names
    imgs_apply -- list of equalized images to be aligned and used later in algorithm
    debug_directory_registered -- directory where registered images should be written
    debug_directory_matches -- directory where match images should be written
    debug -- bool flag indicating whether output images should be saved
    dataset -- robust registration dataset
    dataset_enabled -- bool flag indicating whether robust dataset should be used
    MAX_ROTATION -- maximum allowable rotation
    MAX_TRANSLATION -- maximum allowable translation
    MAX_SCALING -- maximum allowable image scaling
    NUM_STD_DEV -- number of standard deviations away from dataset mean the MSE
        of the affine matrix can be
    params -- registration parameters (e.g. ECC thresholds, ORB features, etc.)
    imageSummary -- dictionary containing information about each run
    misc_params -- contains bool flag indicating whether ssim comparison should
        be performed
    """

    # counter for successful ORB and ECC registrations
    validORB = 0
    validECC = 0

    # get maximum mean squared error
    max_mean_squared_error = getMaxError(MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING)

    # create output list for images
    registeredImages = list()
    images_names_registered = list()

    # contains output data for JSON file
    registration_output = dict()

    # list to hold new MSE values
    MSE_vals = list()

    # create task list for pool
    tasks = list()
    for i, img in enumerate(imgs):
        tasks.append((img, img_names[i], template, template_reduced_noise,
            imgs_apply[i], debug, debug_directory_registered, debug_directory_matches,
            dataset, dataset_enabled, NUM_STD_DEV, max_mean_squared_error,
            MAX_ROTATION, MAX_TRANSLATION, MAX_SCALING, params, misc_params))

    # run tasks using pool
    for i in tqdm.tqdm(pool.imap(unpackArgs, tasks), total=len(tasks)):
        # if image was aligned
        if i[0] is not None:
            # unpack return
            (imgAligned, name, MSE, ORBFlag, ORBMatrix, ECCFlag, ECCMatrix) = i

            # add data to JSON output
            registration_output[name] = {
                "ORB Aligned": ORBFlag,
                "ORB Matrix": ORBMatrix,
                "ECC Aligned": ECCFlag,
                "ECC Matrix": ECCMatrix
            }

            # add images to list
            registeredImages.append(imgAligned)
            images_names_registered.append(name)

            # update registration lists
            validORB += ORBFlag
            validECC += ECCFlag

            # add MSE to list
            MSE_vals.append(MSE)

            # add to individual summary
            imageSummary[name]["Registered"] = True
            imageSummary[name]["ORB Registered"] = ORBFlag
            imageSummary[name]["ORB Matrix"] = np.round(ORBMatrix, 2)
            imageSummary[name]["ECC Registered"] = ECCFlag
            imageSummary[name]["ECC Matrix"] = np.round(ECCMatrix, 2)

        # image wasn't registered
        else:
            # unpack return
            (_, name, MSE, ORBFlag, ORBMatrix, ECCFlag, ECCMatrix) = i

            # add data to JSON output
            registration_output[name] = {
                "ORB Aligned": ORBFlag,
                "ORB Matrix": ORBMatrix,
                "ECC Aligned": ECCFlag,
                "ECC Matrix": ECCMatrix
            }

            # add to individual summary
            imageSummary[name]["Registered"] = False
            imageSummary[name]["ORB Registered"] = ORBFlag
            imageSummary[name]["ORB Matrix"] = np.round(ORBMatrix, 2)
            imageSummary[name]["ECC Registered"] = ECCFlag
            imageSummary[name]["ECC Matrix"] = np.round(ECCMatrix, 2)

    # update dataset
    if params[7]: # update dataset if using robust registration
        print("Updating Dataset...")
        dataset = updateDataset(dataset, MSE_vals, dataset_enabled)
    avg_MSE = sum(MSE_vals) / len(MSE_vals)

    # if in debugging mode
    if(debug):
        # output JSON file
        file = open(debug_directory_registered + 'registered.json', 'w')
        json.dump(registration_output, file, sort_keys=True, indent=4, separators=(',', ': '))

    # return list of registered images
    return registeredImages, dataset, images_names_registered, [validORB, validECC, avg_MSE], imageSummary
